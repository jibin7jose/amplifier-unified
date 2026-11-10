import {startFixture} from './fixture-readiness.mjs';
import {fileURLToPath} from 'node:url';

export async function startSettingsFixture(env={}) {
 return startFixture({
  command:process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),
  args:['-u',fileURLToPath(new URL('../../tests/fixtures/settings_ui_server.py',import.meta.url))],
  env:{...process.env,...env},tag:'settings-ui',name:'Settings',
 });
}
