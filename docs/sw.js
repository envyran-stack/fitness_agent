// Fitness Agent PWA 서비스 워커
//
// 실제 앱 내용은 iframe으로 불러오는 Streamlit Cloud 서버(항상 인터넷 필요)에 있으므로,
// 여기서는 "홈 화면에 추가(설치)" 조건을 만족시키는 최소 서비스 워커 역할만 한다.
// 이 껍데기 페이지(index.html, manifest.json, 아이콘)만 캐시해서, 오프라인일 때도
// 최소한 "인터넷에 연결해 주세요" 안내는 뜰 수 있게 한다.

const CACHE_NAME = "fitness-agent-shell-v1";
const SHELL_ASSETS = [
  "./index.html",
  "./manifest.json",
  "./icons/icon-192.png",
  "./icons/icon-512.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(SHELL_ASSETS))
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys
          .filter((key) => key !== CACHE_NAME)
          .map((key) => caches.delete(key))
      )
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  // 껍데기 페이지 자체(HTML/manifest/아이콘)만 캐시 우선으로 응답한다.
  // 안에 뜨는 실제 앱(iframe)은 항상 네트워크로 새로 받아온다.
  const url = new URL(event.request.url);
  const isShellAsset = SHELL_ASSETS.some((asset) => url.pathname.endsWith(asset.replace("./", "/")));
  if (!isShellAsset) {
    return;
  }
  event.respondWith(
    caches.match(event.request).then((cached) => cached || fetch(event.request))
  );
});
