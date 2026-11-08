import L from "leaflet";

/**
 * 底图来源。
 *
 * - 地图 = OSM 标准瓦片。它本身是亮色，由 `styles/map.css` 的 `.is-dark-base`
 *   统一压暗（`brightness(.7) contrast(1.2)`，与 TrajNexa_Map 暗色模式同一套参数）。
 * - 卫星 = Esri World Imagery，保持原色：影像要当人工判读依据，不做调色。
 *
 * 不用现成的暗色瓦片服务：CARTO 的 `dark_all` 现在要 API Key，无 Key 取到的瓦片会盖满
 * 「API KEY REQUIRED」水印；Esri 的 Dark Gray Canvas 官方只承诺全球 0–10 级，11 级以上
 * 不覆盖中国，而本项目固定在 16–19 级看。压暗滤镜不改变瓦片来源，覆盖与层级跟以前一致。
 *
 * 影像有回退链：Esri 在部分网络（含本项目现场）整段被拒（403 / ERR_ABORTED），
 * 而真值拾取必须看见影像才能判读地物，所以 Esri 连续失败后自动换 Google 影像，
 * 再失败退到 OSM 底图（仍可用，但细节不足以精确指点）。回退只换瓦片地址，
 * 坐标系一律 Web Mercator + WGS84，不做 GCJ-02 纠偏，避免经纬度被平移。
 */

const OSM_TILES = "https://tile.openstreetmap.org/{z}/{x}/{y}.png";

export const ATTRIBUTION = {
  map: "© OpenStreetMap contributors · 镜头数据 TrajNexa_Map",
  imagery: "影像 © Esri · Maxar · Earthstar Geographics",
  imageryGoogle: "影像 © Google",
} as const;

type ImagerySource = { url: string; attribution: string; options?: L.TileLayerOptions };

const IMAGERY_SOURCES: ImagerySource[] = [
  {
    url: "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    attribution: ATTRIBUTION.imagery,
  },
  {
    url: "https://mt{s}.google.com/vt/lyrs=s&x={x}&y={y}&z={z}",
    attribution: ATTRIBUTION.imageryGoogle,
    options: { subdomains: "0123" },
  },
  { url: OSM_TILES, attribution: ATTRIBUTION.map },
];

export function mapTiles(): L.TileLayer {
  return L.tileLayer(OSM_TILES, { maxZoom: 19, attribution: ATTRIBUTION.map });
}

/**
 * 卫星影像：Esri 优先，连续失败自动降级。
 *
 * 降级判据是「首个成功瓦片之前累计 4 次 tileerror」——Esri 正常时首块即成功，
 * 之后的个别失败（缩放边缘、缓存丢失）不会误触发换源。
 */
export function imageryTiles(): L.TileLayer {
  const first = IMAGERY_SOURCES[0];
  const layer = L.tileLayer(first.url, {
    maxZoom: 19,
    attribution: first.attribution,
    ...(first.options ?? {}),
  });

  let index = 0;
  let errors = 0;
  let seenSuccess = false;

  layer.on("tileload", () => {
    seenSuccess = true;
  });

  layer.on("tileerror", () => {
    if (seenSuccess) return;
    errors += 1;
    if (errors < 4 || index >= IMAGERY_SOURCES.length - 1) return;
    const previous = IMAGERY_SOURCES[index];
    index += 1;
    const next = IMAGERY_SOURCES[index];
    errors = 0;
    layer.setUrl(next.url, false);
    layer.options.subdomains = next.options?.subdomains ?? "abc";
    layer.options.attribution = next.attribution;
    const owner = (layer as unknown as { _map?: L.Map })._map;
    if (owner?.attributionControl) {
      owner.attributionControl.removeAttribution(previous.attribution);
      owner.attributionControl.addAttribution(next.attribution);
    }
    // 状态栏的自定义署名要跟着换，否则会写着 Esri 却显示 Google 影像
    layer.fire("basemapchange", { attribution: next.attribution });
    // setUrl 不会重取已报错的瓦片，触发一次可见范围重绘
    layer.redraw();
  });

  return layer;
}

/** 地图 / 卫星两种底图；「地图」由 `.is-dark-base` 压成暗色 */
export function basemapTiles(kind: "map" | "satellite"): L.TileLayer {
  return kind === "satellite" ? imageryTiles() : mapTiles();
}