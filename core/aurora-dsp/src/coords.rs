//! Plan §6.9: +X front, +Y left, +Z up. Azimuth degrees, positive to the left.

pub fn az_el_to_cart(azimuth_deg: f64, elevation_deg: f64, radius: f64) -> [f64; 3] {
    let az = azimuth_deg.to_radians();
    let el = elevation_deg.to_radians();
    let cos_el = el.cos();
    [
        radius * cos_el * az.cos(),
        radius * cos_el * az.sin(),
        radius * el.sin(),
    ]
}

pub fn cart_to_az_el(xyz: [f64; 3]) -> (f64, f64, f64) {
    let r = (xyz[0] * xyz[0] + xyz[1] * xyz[1] + xyz[2] * xyz[2]).sqrt();
    if r < 1e-12 {
        return (0.0, 0.0, 0.0);
    }
    let az = xyz[1].atan2(xyz[0]).to_degrees();
    let el = (xyz[2] / r).clamp(-1.0, 1.0).asin().to_degrees();
    (az, el, r)
}

pub fn angular_distance_deg(az1: f64, el1: f64, az2: f64, el2: f64) -> f64 {
    let a = az_el_to_cart(az1, el1, 1.0);
    let b = az_el_to_cart(az2, el2, 1.0);
    let dot = (a[0] * b[0] + a[1] * b[1] + a[2] * b[2]).clamp(-1.0, 1.0);
    dot.acos().to_degrees()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn front_left_up_match_the_plan() {
        let front = az_el_to_cart(0.0, 0.0, 1.0);
        assert!(front[0] > 0.9 && front[1].abs() < 1e-9 && front[2].abs() < 1e-9);
        let left = az_el_to_cart(90.0, 0.0, 1.0);
        assert!(left[1] > 0.9 && left[0].abs() < 1e-6);
        let up = az_el_to_cart(0.0, 90.0, 1.0);
        assert!(up[2] > 0.9);
        let (az, el, r) = cart_to_az_el(left);
        assert!((az - 90.0).abs() < 1e-6);
        assert!(el.abs() < 1e-6);
        assert!((r - 1.0).abs() < 1e-12);
        assert!(angular_distance_deg(0.0, 0.0, 90.0, 0.0) - 90.0 < 1e-6);
    }
}
