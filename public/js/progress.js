import { createStore } from './store.js';
import { CONFIG } from './config.js';

export const store = createStore({ prefix: CONFIG.storagePrefix });
