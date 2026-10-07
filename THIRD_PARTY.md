# Third-party components

Frontend dependencies are distributed locally, not fetched by the running app.

| Component | Version | License | Source |
| --- | --- | --- | --- |
| @xterm/xterm | 5.5.0 | MIT | https://www.npmjs.com/package/@xterm/xterm/v/5.5.0 |
| @xterm/addon-fit | 0.10.0 | MIT | https://www.npmjs.com/package/@xterm/addon-fit/v/0.10.0 |
| pywinpty | 2.0.15 | MIT | https://pypi.org/project/pywinpty/2.0.15/ |

Vendor files were downloaded from version-pinned jsDelivr npm URLs on 2026-10-07:

- https://cdn.jsdelivr.net/npm/@xterm/xterm@5.5.0/lib/xterm.js
- https://cdn.jsdelivr.net/npm/@xterm/xterm@5.5.0/css/xterm.css
- https://cdn.jsdelivr.net/npm/@xterm/addon-fit@0.10.0/lib/addon-fit.js

Original frontend licenses are included under `static/vendor/LICENSE-xterm` and `static/vendor/LICENSE-addon-fit`. pywinpty is installed separately by pip, with its own package metadata/license. The ZIP does not include the Python interpreter or an existing Conda environment.
