/* 能力探针配套 Service Worker：只为验证注册能力，不做任何缓存 */
"use strict";
self.addEventListener("install", function(){ self.skipWaiting(); });
self.addEventListener("activate", function(e){ e.waitUntil(self.clients.claim()); });
self.addEventListener("fetch", function(){ /* 直通，不拦截 */ });
