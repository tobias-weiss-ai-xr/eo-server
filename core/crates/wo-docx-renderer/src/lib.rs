// wo-docx-renderer -- World-Office DOCX rendering engine
//!
//! Converts DOCX documents to rendered output (PDF, images, etc.).
//! This crate provides the rendering pipeline that transforms DOCX
//! document models into visual output via the wo-renderer backend.

pub mod layout;
pub mod model;
pub mod pipeline;
pub mod roundtrip;

pub use model::{RenderConfig, RenderOutput, RenderResult};
pub use pipeline::DocxRenderPipeline;
pub use roundtrip::DocxRendererRoundtrip;

pub const FORMAT_NAME: &str = "docx-renderer";
