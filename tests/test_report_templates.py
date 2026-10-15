from __future__ import annotations

from kofvision.report_templates import render_report_html


def test_render_report_html_includes_template_and_theme() -> None:
    html = render_report_html(
        title="Demo",
        generated="2026-01-01 00:00:00",
        metadata={"A": 1},
        plan_image_rel="plan.png",
        table_rows_html="<tr><td>x</td></tr>",
        profile_sections_html="<section id='p1'></section>",
        template_name="compact",
        theme_name="dark",
    )
    assert "Template: compact" in html
    assert "Theme: dark" in html
    assert "plan.png" in html
