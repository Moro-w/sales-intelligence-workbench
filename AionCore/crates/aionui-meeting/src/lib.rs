//! Fixed, authenticated Tingji text-mode gateway; no model calls or public proxy.
pub mod routes;
pub mod service;
pub mod state;
pub use routes::domain_routes;
pub use service::MeetingService;
pub use state::MeetingRouterState;
