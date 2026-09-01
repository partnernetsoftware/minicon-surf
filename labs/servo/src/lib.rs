//! Compile-only probe for the public Servo 0.5.0 embedding surface.

use servo::{
    OffscreenRenderingContext, RenderingContext, Servo, ServoBuilder, SoftwareRenderingContext,
    WebView, WebViewBuilder,
};

/// Keeping these public API types in one signature makes `cargo check` fail if
/// the pinned release does not expose the engine, web-view, and detachable
/// rendering concepts documented by Servo.
#[allow(clippy::type_complexity)]
pub fn embedding_api_surface() -> (
    fn() -> ServoBuilder,
    Option<Servo>,
    Option<WebView>,
    Option<WebViewBuilder>,
    Option<Box<dyn RenderingContext>>,
    Option<OffscreenRenderingContext>,
    Option<SoftwareRenderingContext>,
) {
    (ServoBuilder::default, None, None, None, None, None, None)
}

#[cfg(test)]
mod tests {
    #[test]
    fn public_embedding_types_are_linked() {
        let _ = super::embedding_api_surface();
    }
}
