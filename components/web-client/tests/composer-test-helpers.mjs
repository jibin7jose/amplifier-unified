// Bind the one canonical composer test implementation to this owner's Playwright.
import {expect} from '@playwright/test';
import {installDraftMatchers,clearComposerWith} from './composer-test-support.mjs';
export {readComposerDraft,installDraftMatchers} from './composer-test-support.mjs';
installDraftMatchers(expect);
export const clearComposer=locator=>clearComposerWith(expect,locator);
