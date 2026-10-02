# Build and Distribute on Windows (PyInstaller)

This project can be packaged as a standalone Windows app for end users.

## 1. Build prerequisites

Install Python (same major/minor version you want to build with), then from the repo root:

```powershell
python -m pip install -r requirements-build.txt
```

## 2. Build the app

From the repo root, run:

```powershell
.\scripts\build_pyinstaller.ps1
```

Default mode is `onedir`.

### Alternative build mode

Build as single EXE:

```powershell
.\scripts\build_pyinstaller.ps1 -Mode onefile
```

## 3. Output

Build artifacts are written to `dist/`:

- `dist/KOFvision-release/` (folder you can inspect)
- `dist/KOFvision-release-onedir.zip` or `dist/KOFvision-release-onefile.zip` (share this)

## 4. End-user distribution notes

- Distribute the generated ZIP to users.
- Users should extract all files before running.
- If using `onedir`, users must keep the app folder contents together.
- SmartScreen may show a warning for unsigned executables. Code-signing is recommended for production deployment.

## 5. Recommended release checklist

1. Build from a clean virtual environment.
2. Smoke-test `KOFvision.exe` on a machine that does not have your dev environment.
3. Verify file-open, triangulation, volume, and section workflows.
4. Archive the exact ZIP delivered to users.
