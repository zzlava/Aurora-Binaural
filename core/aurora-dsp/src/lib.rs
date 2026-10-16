//! Real-time-safe route B DSP core.
//!
//! Python is not a dependency of this crate. Bindings live in `aurora-py`.

// NumPy reference constants are written out in full, and sample loops index
// several buffers at once. Both are intentional.
#![allow(clippy::excessive_precision, clippy::needless_range_loop)]

pub mod chain;
pub mod conv;
pub mod coords;
pub mod crossover;
pub mod decorrelate;
pub mod early;
pub mod error;
pub mod fdn;
pub mod guard;
pub mod hrir;
pub mod interpolate;
pub mod limiter;
pub mod params;
pub mod processor;
pub mod rng;
pub mod upmix;
mod ziggurat_tables;

pub use chain::RouteB;
pub use processor::{Params, Processor, RenderConfig};

#[cfg(test)]
pub mod test_alloc {
    use std::alloc::{GlobalAlloc, Layout, System};
    use std::cell::Cell;
    use std::sync::atomic::{AtomicUsize, Ordering};

    pub static COUNT: AtomicUsize = AtomicUsize::new(0);
    thread_local! {
        static ENABLED: Cell<bool> = const { Cell::new(false) };
    }

    pub fn set_enabled(enabled: bool) {
        ENABLED.with(|flag| flag.set(enabled));
    }

    fn counting() -> bool {
        ENABLED.try_with(|flag| flag.get()).unwrap_or(false)
    }

    pub struct Counting;

    unsafe impl GlobalAlloc for Counting {
        unsafe fn alloc(&self, layout: Layout) -> *mut u8 {
            if counting() {
                COUNT.fetch_add(1, Ordering::Relaxed);
            }
            unsafe { System.alloc(layout) }
        }

        unsafe fn alloc_zeroed(&self, layout: Layout) -> *mut u8 {
            if counting() {
                COUNT.fetch_add(1, Ordering::Relaxed);
            }
            unsafe { System.alloc_zeroed(layout) }
        }

        unsafe fn dealloc(&self, ptr: *mut u8, layout: Layout) {
            unsafe { System.dealloc(ptr, layout) }
        }

        unsafe fn realloc(&self, ptr: *mut u8, layout: Layout, new_size: usize) -> *mut u8 {
            if counting() {
                COUNT.fetch_add(1, Ordering::Relaxed);
            }
            unsafe { System.realloc(ptr, layout, new_size) }
        }
    }
}

#[cfg(test)]
#[global_allocator]
static GLOBAL: test_alloc::Counting = test_alloc::Counting;
