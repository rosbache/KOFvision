from __future__ import annotations

import html
from typing import Any


REPORT_THEMES: dict[str, dict[str, str]] = {
    "light": {
        "bg": "#ffffff",
        "fg": "#1f2a30",
        "muted": "#546572",
        "border": "#d6dee3",
        "header": "#f3f7f9",
    },
    "dark": {
        "bg": "#121a20",
        "fg": "#e5edf2",
        "muted": "#9fb3c1",
        "border": "#2f3d47",
        "header": "#1f2a33",
    },
    "print": {
        "bg": "#ffffff",
        "fg": "#0f1720",
        "muted": "#4b5563",
        "border": "#d1d5db",
        "header": "#f8fafc",
    },
}

REPORT_TEMPLATES = {"standard", "compact"}


def render_report_html(
    *,
    title: str,
    generated: str,
    metadata: dict[str, Any],
    plan_image_rel: str,
    table_rows_html: str,
    profile_sections_html: str,
    template_name: str,
    theme_name: str,
) -> str:
    template = template_name if template_name in REPORT_TEMPLATES else "standard"
    theme = REPORT_THEMES.get(theme_name, REPORT_THEMES["light"])

    meta_items = "\n".join(
        f"<li><strong>{html.escape(str(k))}</strong>: {html.escape(str(v))}</li>"
        for k, v in metadata.items()
    )

    compact_style = ""
    if template == "compact":
        compact_style = """
    body { margin: 12px; }
    .profile { margin: 0.8rem 0 1.1rem; }
    th, td { padding: 0.28rem 0.36rem; font-size: 0.92rem; }
    """

    return f"""<!doctype html>
<html lang='en'>
<head>
  <meta charset='utf-8' />
  <meta name='viewport' content='width=device-width, initial-scale=1' />
  <title>{html.escape(title)}</title>
  <style>
    body {{ font-family: Segoe UI, Arial, sans-serif; margin: 18px; color: {theme['fg']}; background: {theme['bg']}; }}
    h1, h2 {{ margin-bottom: 0.4rem; }}
    p, li {{ color: {theme['muted']}; }}
    .meta li {{ margin: 0.2rem 0; }}
    table {{ border-collapse: collapse; width: 100%; margin: 1rem 0 1.5rem; }}
    th, td {{ border: 1px solid {theme['border']}; padding: 0.4rem 0.5rem; text-align: right; }}
    th:first-child, td:first-child {{ text-align: left; }}
    th {{ background: {theme['header']}; }}
    img {{ max-width: 100%; height: auto; border: 1px solid {theme['border']}; }}
    .profile {{ margin: 1.2rem 0 1.8rem; }}
    {compact_style}
  </style>
</head>
<body>
  <h1>{html.escape(title)}</h1>
  <p>Generated: {html.escape(generated)} | Template: {html.escape(template)} | Theme: {html.escape(theme_name)}</p>
  <h2>Metadata</h2>
  <ul class='meta'>
    {meta_items}
  </ul>

  <h2>Plan View</h2>
  <img src='{html.escape(plan_image_rel)}' alt='Plan view with baseline and profiles' />

  <h2>Profile Summary</h2>
  <table>
    <thead>
      <tr>
        <th>Profile</th>
        <th>Station (m)</th>
        <th>Length (m)</th>
        <th>Valid</th>
        <th>Area Net (m2)</th>
        <th>Cut (m2)</th>
        <th>Fill (m2)</th>
        <th>Top-Intermediate (m2)</th>
        <th>Intermediate-Bottom (m2)</th>
        <th>Vol Net (m3)</th>
        <th>Vol Cut (m3)</th>
        <th>Vol Fill (m3)</th>
        <th>Vol Top-Intermediate (m3)</th>
        <th>Vol Top-Intermediate Cut (m3)</th>
        <th>Vol Top-Intermediate Fill (m3)</th>
        <th>Vol Intermediate-Bottom (m3)</th>
        <th>Vol Intermediate-Bottom Cut (m3)</th>
        <th>Vol Intermediate-Bottom Fill (m3)</th>
      </tr>
    </thead>
    <tbody>
      {table_rows_html}
    </tbody>
  </table>

  <h2>Profiles</h2>
  {profile_sections_html}
</body>
</html>
"""
