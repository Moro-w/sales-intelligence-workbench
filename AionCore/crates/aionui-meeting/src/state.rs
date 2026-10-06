use crate::service::MeetingService;
use std::sync::Arc;

#[derive(Clone, Default)]
pub struct MeetingRouterState {
    pub service: Option<Arc<MeetingService>>,
}
