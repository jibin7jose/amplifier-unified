import {startFixture} from './fixture-readiness.mjs';
import {fileURLToPath} from 'node:url';

export async function startChatFixture() {
 return startFixture({
  command:process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),
  args:['-u',fileURLToPath(new URL('../../tests/fixtures/chat_ui_server.py',import.meta.url)),'--dynamic-port'],
  env:process.env,tag:'chat-ui',name:'Chat',
 });
}
