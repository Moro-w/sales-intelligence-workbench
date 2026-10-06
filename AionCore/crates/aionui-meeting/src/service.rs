use std::time::Duration;

pub const MAX_REQUEST: usize = 1024 * 1024 + 16384;
// Full original + UTF-16 indexes + cleaned rows can exceed the raw upload size many times.
const MAX_RESPONSE: usize = 32 * 1024 * 1024;

pub struct MeetingService {
    port: u16,
    token: String,
    client: reqwest::Client,
}

pub struct ProxyReply {
    pub status: u16,
    pub content_type: String,
    pub disposition: Option<String>,
    pub body: Vec<u8>,
}

#[derive(Debug, PartialEq)]
pub enum MeetingError {
    NotAllowed,
    TooLarge,
    Unavailable,
}

/// Whitelist native Tingji contracts. No arbitrary URL, filesystem path, model,
/// audio, settings or directory-browser endpoint can pass through this gate.
pub fn upstream_path(method: &str, path: &str) -> Option<String> {
    let valid_id = |id: &str| id.len() == 32 && id.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b));
    let parts: Vec<_> = path.split('/').collect();
    match (method, path, parts.as_slice()) {
        ("GET", "status", _) => Some("/api/status".into()),
        ("GET", "meetings", _) => Some("/api/meetings".into()),
        ("POST", "imports", _) => Some("/api/imports".into()),
        ("GET", "ui/", _) => Some("/ui/".into()),
        ("GET", _, ["meetings", id]) if valid_id(id) => Some(format!("/api/meetings/{id}")),
        ("GET", _, ["meetings", id, "source"]) if valid_id(id) => Some(format!("/api/meetings/{id}/source")),
        ("GET", _, ["meetings", id, "job"]) if valid_id(id) => Some(format!("/api/meetings/{id}/job")),
        ("GET", _, ["meetings", id, "reference"]) if valid_id(id) => Some(format!("/api/meetings/{id}/reference")),
        ("POST", _, ["meetings", id, action])
            if valid_id(id) && ["process", "save", "draft", "suggestion", "archive"].contains(action) =>
        {
            Some(format!("/api/meetings/{id}/{action}"))
        }
        ("GET", _, ["ui", "m", id]) if valid_id(id) => Some(format!("/ui/m/{id}")),
        ("GET", _, ["ui", "static", name])
            if [
                "style.css",
                "meeting.css",
                "common.js",
                "app.js",
                "meeting.js",
                "marked.min.js",
                "text-mode.js",
                "text-mode.css",
                "text-results.js",
                "text-edit.js",
                "text-export.js",
            ]
            .contains(name) =>
        {
            Some(format!("/ui/static/{name}"))
        }
        _ => None,
    }
}

impl MeetingService {
    pub fn new(port: u16, token: String) -> Result<Self, reqwest::Error> {
        let client = reqwest::Client::builder()
            .no_proxy()
            .redirect(reqwest::redirect::Policy::none())
            .timeout(Duration::from_secs(30))
            .build()?;
        Ok(Self { port, token, client })
    }

    pub async fn forward(
        &self,
        method: &str,
        path: &str,
        owner: &str,
        content_type: Option<&str>,
        body: Vec<u8>,
    ) -> Result<ProxyReply, MeetingError> {
        let target = upstream_path(method, path).ok_or(MeetingError::NotAllowed)?;
        if body.len() > MAX_REQUEST {
            return Err(MeetingError::TooLarge);
        }
        let method = reqwest::Method::from_bytes(method.as_bytes()).map_err(|_| MeetingError::NotAllowed)?;
        let mut request = self
            .client
            .request(method, format!("http://127.0.0.1:{}{}", self.port, target))
            .header("x-tingji-service-token", &self.token)
            .header("x-tingji-user-id", owner);
        if let Some(value) = content_type {
            request = request.header("content-type", value);
        }
        // Cookies, browser authorization and arbitrary client headers are NOT forwarded.
        let mut response = request.body(body).send().await.map_err(|_| MeetingError::Unavailable)?;
        if response.status().is_redirection() || response.content_length().is_some_and(|n| n > MAX_RESPONSE as u64) {
            return Err(MeetingError::Unavailable);
        }
        let status = response.status().as_u16();
        let content_type = response
            .headers()
            .get("content-type")
            .and_then(|h| h.to_str().ok())
            .unwrap_or("application/octet-stream")
            .to_owned();
        let disposition = response
            .headers()
            .get("content-disposition")
            .and_then(|h| h.to_str().ok())
            .map(str::to_owned);
        let mut body = Vec::new();
        while let Some(chunk) = response.chunk().await.map_err(|_| MeetingError::Unavailable)? {
            if body.len() + chunk.len() > MAX_RESPONSE {
                return Err(MeetingError::TooLarge);
            }
            body.extend_from_slice(&chunk);
        }
        Ok(ProxyReply {
            status,
            content_type,
            disposition,
            body,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn only_expected_contracts_are_routed() {
        assert_eq!(upstream_path("POST", "imports"), Some("/api/imports".into()));
        assert_eq!(
            upstream_path("GET", &format!("meetings/{}/source", "a".repeat(32))),
            Some(format!("/api/meetings/{}/source", "a".repeat(32)))
        );
        assert!(upstream_path("GET", "ui/static/text-mode.js").is_some());
        for action in ["save", "draft", "suggestion", "archive"] {
            let path = format!("meetings/{}/{action}", "a".repeat(32));
            assert_eq!(upstream_path("POST", &path), Some(format!("/api/{path}")));
            assert_eq!(upstream_path("GET", &path), None);
        }
        for name in ["text-edit.js", "text-export.js"] {
            assert!(upstream_path("GET", &format!("ui/static/{name}")).is_some());
        }
    }
    #[test]
    fn traversal_audio_settings_and_source_mutation_are_denied() {
        for p in [
            "ui/static/../config.yaml",
            "ui/static/%2e%2e%2fconfig.yaml",
            "settings",
            "upload",
            "meetings/../source",
            "http://evil.test/",
            "ui/static/access.js",
        ] {
            assert_eq!(upstream_path("GET", p), None, "{p}");
        }
        assert_eq!(
            upstream_path("PUT", &format!("meetings/{}/source", "b".repeat(32))),
            None
        );
        assert_eq!(upstream_path("POST", "status"), None);
    }
    #[tokio::test]
    async fn oversize_is_rejected_before_network() {
        let service = MeetingService::new(1, "private".into()).unwrap();
        assert!(matches!(
            service
                .forward("POST", "imports", "user", None, vec![0; MAX_REQUEST + 1])
                .await,
            Err(MeetingError::TooLarge)
        ));
    }
    #[tokio::test]
    async fn down_service_returns_sanitized_error() {
        let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        drop(listener);
        let service = MeetingService::new(port, "do-not-leak".into()).unwrap();
        assert!(matches!(
            service.forward("GET", "status", "user", None, vec![]).await,
            Err(MeetingError::Unavailable)
        ));
    }
}
