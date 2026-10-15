# task_executor.py

## Role

Run heavy computations on background threads while safely applying results on the Tk UI thread.

## Responsibilities

- Submit background callables.
- Marshal completion callbacks to the Tk event loop using `after(0, ...)`.
- Keep UI responsive during expensive volume/report operations.

## Current Usage

- Volume compute path (`_on_compute_volume`) uses async task submission.
- Section report export/browser generation uses async task submission.

## Safety Rules

- Worker tasks must not call Tk widgets directly.
- UI updates and message boxes happen only in completion/error callbacks.
