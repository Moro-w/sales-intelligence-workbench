//! Opaque Tingji response contracts intentionally remain native for page reuse.
use crate::{
    service::{MAX_REQUEST, MeetingError, upstream_path},
    state::MeetingRouterState,
};
use aionui_auth::CurrentUser;
use axum::{
    Router,
    body::{Body, to_bytes},
    extract::{Extension, Request, State},
    http::{StatusCode, header},
    response::{IntoResponse, Response},
    routing::any,
};

pub fn domain_routes(state: MeetingRouterState) -> Router {
    Router::new()
        .route("/api/meeting-assistant/{*path}", any(proxy))
        .with_state(state)
}

async fn proxy(
    State(state): State<MeetingRouterState>,
    Extension(user): Extension<CurrentUser>,
    request: Request,
) -> Response {
    let path = request
        .uri()
        .path()
        .strip_prefix("/api/meeting-assistant/")
        .unwrap_or("")
        .to_owned();
    let method = request.method().as_str().to_owned();
    if upstream_path(&method, &path).is_none() {
        return (StatusCode::NOT_FOUND, "Meeting endpoint not available").into_response();
    }
    let Some(service) = state.service else {
        return (StatusCode::SERVICE_UNAVAILABLE, "Meeting service unavailable").into_response();
    };
    let content_type = request
        .headers()
        .get(header::CONTENT_TYPE)
        .and_then(|h| h.to_str().ok())
        .map(str::to_owned);
    let body = match to_bytes(request.into_body(), MAX_REQUEST).await {
        Ok(bytes) => bytes.to_vec(),
        Err(_) => return (StatusCode::PAYLOAD_TOO_LARGE, "Meeting upload too large").into_response(),
    };
    match service
        .forward(&method, &path, &user.id, content_type.as_deref(), body)
        .await
    {
        Ok(reply) => {
            let mut response = Response::builder()
                .status(reply.status)
                .header(header::CONTENT_TYPE, reply.content_type)
                .header(header::CACHE_CONTROL, "no-store");
            if let Some(value) = reply.disposition {
                response = response.header(header::CONTENT_DISPOSITION, value);
            }
            response
                .body(Body::from(reply.body))
                .unwrap_or_else(|_| StatusCode::BAD_GATEWAY.into_response())
        }
        Err(MeetingError::NotAllowed) => StatusCode::NOT_FOUND.into_response(),
        Err(MeetingError::TooLarge) => StatusCode::PAYLOAD_TOO_LARGE.into_response(),
        Err(MeetingError::Unavailable) => {
            tracing::warn!(code = "MEETING_SERVICE_UNAVAILABLE", "Meeting service unavailable");
            (StatusCode::BAD_GATEWAY, "Meeting service unavailable").into_response()
        }
    }
}
