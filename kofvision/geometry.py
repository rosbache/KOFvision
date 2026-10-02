from __future__ import annotations

import numpy as np

_EPS = 1e-12


def poly_signed_area(poly: list[np.ndarray]) -> float:
    if len(poly) < 3:
        return 0.0
    acc = 0.0
    for i, p in enumerate(poly):
        q = poly[(i + 1) % len(poly)]
        acc += p[0] * q[1] - p[1] * q[0]
    return 0.5 * acc


def line_intersection(p1: np.ndarray, p2: np.ndarray, a: np.ndarray, b: np.ndarray) -> np.ndarray:
    r = p2 - p1
    s = b - a
    denom = r[0] * s[1] - r[1] * s[0]
    if abs(denom) < _EPS:
        return p2.copy()
    ap = a - p1
    t = (ap[0] * s[1] - ap[1] * s[0]) / denom
    return p1 + t * r


def clip_polygon_convex(subject: list[np.ndarray], clipper: list[np.ndarray]) -> list[np.ndarray]:
    if len(subject) < 3 or len(clipper) < 3:
        return []

    output = [p.copy() for p in subject]
    clip_sign = 1.0 if poly_signed_area(clipper) >= 0.0 else -1.0
    ccount = len(clipper)

    for i in range(ccount):
        if len(output) < 3:
            return []
        a = clipper[i]
        b = clipper[(i + 1) % ccount]

        def inside(pt: np.ndarray) -> bool:
            edge = b - a
            rel = pt - a
            cross = edge[0] * rel[1] - edge[1] * rel[0]
            return clip_sign * cross >= -_EPS

        input_poly = output
        output = []
        prev = input_poly[-1]
        prev_in = inside(prev)

        for curr in input_poly:
            curr_in = inside(curr)
            if curr_in:
                if not prev_in:
                    output.append(line_intersection(prev, curr, a, b))
                output.append(curr.copy())
            elif prev_in:
                output.append(line_intersection(prev, curr, a, b))
            prev = curr
            prev_in = curr_in
    return output


def point_in_triangle(pt: np.ndarray, tri: list[np.ndarray]) -> bool:
    a, b, c = tri
    v0 = c - a
    v1 = b - a
    v2 = pt - a
    den = v0[0] * v1[1] - v1[0] * v0[1]
    if abs(den) < _EPS:
        return False
    u = (v2[0] * v1[1] - v1[0] * v2[1]) / den
    v = (v0[0] * v2[1] - v2[0] * v0[1]) / den
    return u >= -_EPS and v >= -_EPS and (u + v) <= 1.0 + _EPS


def triangulate_simple_polygon(poly: list[np.ndarray]) -> list[list[np.ndarray]]:
    if len(poly) < 3:
        return []
    pts = [p.copy() for p in poly]
    if np.linalg.norm(pts[0] - pts[-1]) < _EPS:
        pts.pop()
    if len(pts) < 3:
        return []

    sign = 1.0 if poly_signed_area(pts) >= 0.0 else -1.0
    indices = list(range(len(pts)))
    triangles: list[list[np.ndarray]] = []
    guard = 0

    while len(indices) > 3 and guard < len(pts) * len(pts):
        guard += 1
        ear_found = False
        n = len(indices)
        for i in range(n):
            i0 = indices[(i - 1) % n]
            i1 = indices[i]
            i2 = indices[(i + 1) % n]
            a = pts[i0]
            b = pts[i1]
            c = pts[i2]

            cross = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
            if sign * cross <= _EPS:
                continue

            tri = [a, b, c]
            contains_other = False
            for j in indices:
                if j in (i0, i1, i2):
                    continue
                if point_in_triangle(pts[j], tri):
                    contains_other = True
                    break
            if contains_other:
                continue

            triangles.append([a.copy(), b.copy(), c.copy()])
            indices.pop(i)
            ear_found = True
            break

        if not ear_found:
            return []

    if len(indices) == 3:
        triangles.append([pts[indices[0]].copy(), pts[indices[1]].copy(), pts[indices[2]].copy()])
    return triangles


def plane_coeff(tri_xy: np.ndarray, tri_z: np.ndarray) -> np.ndarray:
    m = np.column_stack([tri_xy[:, 0], tri_xy[:, 1], np.ones(3)])
    return np.linalg.solve(m, tri_z)


def integrate_linear_over_polygon(poly: list[np.ndarray], coeff: np.ndarray) -> tuple[float, float]:
    if len(poly) < 3:
        return 0.0, 0.0
    p0 = poly[0]

    def f(pt: np.ndarray) -> float:
        return float(coeff[0] * pt[0] + coeff[1] * pt[1] + coeff[2])

    f0 = f(p0)
    integral = 0.0
    area = 0.0
    for i in range(1, len(poly) - 1):
        p1 = poly[i]
        p2 = poly[i + 1]
        tri_area = abs(0.5 * ((p1[0] - p0[0]) * (p2[1] - p0[1]) - (p1[1] - p0[1]) * (p2[0] - p0[0])))
        if tri_area <= _EPS:
            continue
        f1 = f(p1)
        f2 = f(p2)
        integral += tri_area * (f0 + f1 + f2) / 3.0
        area += tri_area
    return integral, area


def clip_polygon_by_levelset(poly: list[np.ndarray], coeff: np.ndarray, keep_positive: bool) -> list[np.ndarray]:
    if len(poly) < 3:
        return []

    def g(pt: np.ndarray) -> float:
        return float(coeff[0] * pt[0] + coeff[1] * pt[1] + coeff[2])

    def inside(val: float) -> bool:
        return val >= -_EPS if keep_positive else val <= _EPS

    out: list[np.ndarray] = []
    prev = poly[-1]
    g_prev = g(prev)
    prev_in = inside(g_prev)

    for curr in poly:
        g_curr = g(curr)
        curr_in = inside(g_curr)

        if prev_in != curr_in:
            denom = g_prev - g_curr
            if abs(denom) > _EPS:
                t = g_prev / denom
                t = min(max(t, 0.0), 1.0)
                out.append(prev + t * (curr - prev))
        if curr_in:
            out.append(curr.copy())
        prev = curr
        g_prev = g_curr
        prev_in = curr_in
    return out


def integrate_diff_over_polygon(
    poly: list[np.ndarray],
    top_xy: np.ndarray,
    top_z: np.ndarray,
    bot_xy: np.ndarray,
    bot_z: np.ndarray,
) -> tuple[float, float, float, float]:
    coeff = plane_coeff(top_xy, top_z) - plane_coeff(bot_xy, bot_z)

    net, area = integrate_linear_over_polygon(poly, coeff)
    pos_poly = clip_polygon_by_levelset(poly, coeff, keep_positive=True)
    neg_poly = clip_polygon_by_levelset(poly, coeff, keep_positive=False)

    cut_int, _ = integrate_linear_over_polygon(pos_poly, coeff)
    fill_int, _ = integrate_linear_over_polygon(neg_poly, coeff)
    cut = max(0.0, cut_int)
    fill = max(0.0, -fill_int)
    return net, cut, fill, area
