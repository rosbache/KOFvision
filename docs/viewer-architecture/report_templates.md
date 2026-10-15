# report_templates.py

## Role

Render section HTML reports using selectable templates and color themes.

## Supported Templates

- `standard`
- `compact`

## Supported Themes

- `light`
- `dark`
- `print`

## Integration

- `section_report.write_html_report` delegates final HTML composition to `render_report_html`.
- Viewer controls expose template and theme selectors in the Sections panel.
- `auto` report theme maps to current app theme (`dark` or `light`).

## Extension Guidance

- Add new templates through the renderer while keeping table/profile payload contract unchanged.
- Add theme tokens as simple color dictionaries to preserve portability.
