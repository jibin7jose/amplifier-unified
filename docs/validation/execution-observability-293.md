# Execution observability acceptance (#293)

Owner: Web client execution inspection. The additive `execution-observability`
scenario in `tests/web/browser-checks.json` runs in the existing extended
browser owner on affected changes and background post-release verification. It
is not a new foreground release gate. The historical 65-scenario baseline is
unchanged. The selected-only export replaces the previous full usage export;
existing Chat info and feedback scenarios assert the new safe projection.

Run in an owned DTU with synthetic state and prepared project dependencies:

```sh
python -m pytest tests/test_execution_observability.py tests/test_capacity.py tests/test_browser_check_runner.py
node --test .build/web-client/tests/execution-observability*.test.mjs .build/web-client/tests/conversation-usage.test.mjs .build/web-client/tests/usage-details.test.mjs .build/web-client/tests/timeline.test.mjs
python scripts/web_client_build.py build
node tests/web/execution-observability-browser.mjs
node tests/web/usage-details-browser.mjs
node tests/web/standard-ux-chat-info-browser.mjs
```

The browser fixture allocates a loopback port and disposable history; the script
closes its browser and listener. `AMPLIFIER_TEST_OUTPUT_DIR` retains screenshots,
selected JSON and the result receipt. `AMPLIFIER_TEST_PYTHON` may select the owned
prepared Python environment. Missing browser/dependencies are failures, not skips.
No paid provider, native OS prompt, microphone, notification, or user account is
required. ARM DTU qualification uses the existing Chromium single-process launch
preload because this environment's normal multiprocess launch is unreliable;
that is an environment limit, not native macOS/Windows acceptance.

| ID | Flow and evidence | Current behavior / regression baseline | Desired result |
| --- | --- | --- | --- |
| EO-01 | Appearance → Execution metrics; change scopes and fields, reload | Separate step/Work/conversation choices and fields persist through the same revision-bound shell actions available to agents | No lost choices on reload, restart or overall detail change |
| EO-02 | Open model and Work details | Selected metrics only; status, errors and controls remain; existing cache accounting and narrow layout retained | No double counting; excluded metrics absent |
| EO-03 | Turn on compact conversation status | No automatic capacity request; one request after Read usage, no request on draft/render update | Zero background usage polling or eager history work |
| EO-04 | Copy and download selected conversation metrics | Clipboard and JSON file match; selected fields plus identity, timestamp, state and coverage | No captured requests, private reasoning or credentials |
| EO-05 | Missing, live, reported zero, partial, estimated, cancelled and child-attributed receipts | Pure projection/renderer tests and existing canonical capacity tests preserve distinctions and attribution | Unavailable never silently becomes zero; repeated rendering adds no usage |
| EO-06 | Switch chat while usage read is pending | Late previous-chat result fenced; hidden totals cause no read; request errors remain retryable | Never display the previous chat's totals |
| EO-07 | Context/queue/retry status | Explicitly unavailable; cumulative input and configured budgets are not context-window measurements | Add fields only when canonical normalized telemetry exists |

Browser timings are written to `result.json` as observations, not hard machine-
independent gates. The interaction goal is a sub-500ms local explicit usage read
on the synthetic fixture; a populated production history is a separate scale
qualification. No claim of provider latency or complete historical telemetry is
made. The legacy usage browser also covers light/dark, all detail levels, narrow
layout, reported zero and partial cache coverage without reading raw recordings.
