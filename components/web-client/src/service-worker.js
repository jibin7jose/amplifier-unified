/* Only public branding and the offline help page are cached. No app shell,
   authenticated pages, API responses, chats, attachments, or canvas documents. */
const CACHE_PREFIX='amplifier-public-';
const CACHE=CACHE_PREFIX+'__PUBLIC_ASSET_VERSION__';
const PUBLIC_FILES=[
  '/offline.html','/app-pages.css','/pwa.js','/manifest.webmanifest','/favicon.ico',
  '/branding/favicons/favicon.ico','/branding/favicons/favicon-32.png',
  '/branding/favicons/apple-touch-icon.png','/branding/icons/amplifier-icon-128.png',
  '/branding/pwa/pwa-192.png','/branding/pwa/pwa-512.png',
];
self.addEventListener('install',event=>{
  event.waitUntil(caches.open(CACHE).then(cache=>cache.addAll(PUBLIC_FILES)).then(()=>self.skipWaiting()));
});
self.addEventListener('activate',event=>{
  event.waitUntil(caches.keys().then(keys=>Promise.all(keys.filter(key=>key.startsWith(CACHE_PREFIX)&&key!==CACHE).map(key=>caches.delete(key)))).then(()=>self.clients.claim()));
});
self.addEventListener('fetch',event=>{
  const request=event.request,url=new URL(request.url);
  if(request.method!=='GET'||url.origin!==self.location.origin)return;
  if(PUBLIC_FILES.includes(url.pathname)){
    event.respondWith(caches.open(CACHE).then(async cache=>(await cache.match(url.pathname))||fetch(request)));
    return;
  }
  // Never substitute the offline page for a canvas iframe, API, or login POST.
  if(request.mode==='navigate'&&request.destination==='document'&&['/','/login'].includes(url.pathname)){
    event.respondWith(fetch(request).catch(async()=>{
      const cached=await (await caches.open(CACHE)).match('/offline.html');
      return cached||new Response('Amplifier is unavailable. Start the Python service and try again.',{status:503,headers:{'Content-Type':'text/plain'}});
    }));
  }
});
