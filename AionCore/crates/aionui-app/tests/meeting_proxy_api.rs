mod common;
use axum::{
    body::Body,
    http::{Request, StatusCode},
};
use common::*;
use std::sync::Arc;
use tower::ServiceExt;
use wiremock::{
    Mock, MockServer, ResponseTemplate,
    matchers::{header, method, path},
};

#[tokio::test]
async fn meeting_proxy_uses_user_auth_csrf_and_trusted_owner() {
    let upstream = MockServer::start().await;
    let (_unused, mut services) = build_app().await;
    services.meeting_service = Some(Arc::new(
        aionui_meeting::MeetingService::new(upstream.address().port(), "service-only-token".into()).unwrap(),
    ));
    let mut app = aionui_app::create_router(&services).await.unwrap();
    let (token, csrf) = setup_and_login(&mut app, &services, "admin", "Meeting-test-password-123!").await;

    assert_eq!(
        app.clone()
            .oneshot(get_request("/api/meeting-assistant/status"))
            .await
            .unwrap()
            .status(),
        StatusCode::UNAUTHORIZED
    );
    for endpoint in [
        "imports".to_owned(),
        format!("meetings/{}/process", "a".repeat(32)),
        format!("meetings/{}/save", "a".repeat(32)),
        format!("meetings/{}/draft", "a".repeat(32)),
        format!("meetings/{}/suggestion", "a".repeat(32)),
    ] {
        let missing_csrf = Request::builder()
            .method("POST")
            .uri(format!("/api/meeting-assistant/{endpoint}"))
            .header("authorization", format!("Bearer {token}"))
            .body(Body::from("{}"))
            .unwrap();
        assert_eq!(
            app.clone().oneshot(missing_csrf).await.unwrap().status(),
            StatusCode::FORBIDDEN
        );
    }

    Mock::given(method("GET"))
        .and(path("/api/status"))
        .and(header("x-tingji-user-id", "system_default_user"))
        .and(header("x-tingji-service-token", "service-only-token"))
        .respond_with(ResponseTemplate::new(200).set_body_json(serde_json::json!({"model_enabled": false})))
        .expect(1)
        .mount(&upstream)
        .await;
    let mut request = get_with_token("/api/meeting-assistant/status", &token);
    request
        .headers_mut()
        .insert("x-tingji-user-id", "spoofed-user".parse().unwrap());
    request
        .headers_mut()
        .insert("x-tingji-service-token", "spoofed-token".parse().unwrap());
    let response = app.clone().oneshot(request).await.unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    assert_eq!(body_json(response).await["model_enabled"], false);

    // A valid CSRF mutation reaches only the whitelisted fixed endpoint.
    Mock::given(method("POST"))
        .and(path("/api/imports"))
        .respond_with(ResponseTemplate::new(201).set_body_json(serde_json::json!({"created": true})))
        .expect(1)
        .mount(&upstream)
        .await;
    let response = app
        .clone()
        .oneshot(json_with_token(
            "POST",
            "/api/meeting-assistant/imports",
            serde_json::json!({}),
            &token,
            &csrf,
        ))
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::CREATED);
    let process_path = format!("/api/meetings/{}/process", "a".repeat(32));
    Mock::given(method("POST"))
        .and(path(&process_path))
        .respond_with(ResponseTemplate::new(202).set_body_json(serde_json::json!({"state":"queued"})))
        .expect(1)
        .mount(&upstream)
        .await;
    let response = app
        .clone()
        .oneshot(json_with_token(
            "POST",
            &format!("/api/meeting-assistant/meetings/{}/process", "a".repeat(32)),
            serde_json::json!({}),
            &token,
            &csrf,
        ))
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::ACCEPTED);
    let reference_path = format!("/api/meetings/{}/reference", "a".repeat(32));
    Mock::given(method("GET"))
        .and(path(&reference_path))
        .and(header("x-tingji-user-id", "system_default_user"))
        .respond_with(ResponseTemplate::new(200).set_body_json(serde_json::json!({"original":"private fixture"})))
        .expect(1)
        .mount(&upstream)
        .await;
    let reference_url = format!("/api/meeting-assistant/meetings/{}/reference", "a".repeat(32));
    assert_eq!(
        app.clone().oneshot(get_request(&reference_url)).await.unwrap().status(),
        StatusCode::UNAUTHORIZED
    );
    let mut reference_request = get_with_token(&reference_url, &token);
    reference_request
        .headers_mut()
        .insert("x-tingji-user-id", "other-owner".parse().unwrap());
    let reference_response = app.clone().oneshot(reference_request).await.unwrap();
    assert_eq!(body_json(reference_response).await["original"], "private fixture");
    let requests = upstream.received_requests().await.unwrap();
    for request in requests {
        assert!(!request.headers.contains_key("authorization"));
        assert!(!request.headers.contains_key("cookie"));
    }
    for endpoint in ["settings", "upload", "ui/static/access.js", "meetings/not-an-id"] {
        let response = app
            .clone()
            .oneshot(get_with_token(&format!("/api/meeting-assistant/{endpoint}"), &token))
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::NOT_FOUND);
    }
    Mock::given(method("GET"))
        .and(path("/ui/"))
        .respond_with(
            ResponseTemplate::new(200)
                .set_body_string("<html>Native Tingji</html>")
                .insert_header("content-type", "text/html"),
        )
        .expect(1)
        .mount(&upstream)
        .await;
    let response = app
        .clone()
        .oneshot(get_with_token("/api/meeting-assistant/ui/", &token))
        .await
        .unwrap();
    assert_eq!(response.headers()["x-frame-options"], "SAMEORIGIN");
    assert_eq!(response.headers()["content-security-policy"], "frame-ancestors 'self'");
    assert_eq!(response.headers()["cache-control"], "no-store");
    upstream.verify().await;
}
