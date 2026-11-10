// Keep browser matchers in the calling owner's Playwright instance.
import {expect} from '@playwright/test';
import {showComposerOptionWith} from '../../.build/web-client/tests/composer-options-support.mjs';
export const showComposerOption=(page,section)=>showComposerOptionWith(expect,page,section);
