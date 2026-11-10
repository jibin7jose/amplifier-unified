// Keep browser matchers in the calling owner's Playwright instance.
import {expect} from '@playwright/test';
import {showComposerOptionWith} from './composer-options-support.mjs';
export const showComposerOption=(page,section)=>showComposerOptionWith(expect,page,section);
