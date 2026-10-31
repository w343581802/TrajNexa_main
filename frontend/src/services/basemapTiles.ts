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
 */

const OSM_TILES = "https://tile.openstreetmap.org/{z}/{x}/{y}.png";
const IMAGERY_TILES = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}";

export const ATTRIBUTION = {
  map: "© OpenStreetMap contributors · 镜头数据 TrajNexa_Map",
  imagery: "影像 © Esri · Maxar · Earthstar Geographics",
} as const;

export function mapTiles(): L.TileLayer {
  return L.tileLayer(OSM_TILES, { maxZoom: 19, attribution: ATTRIBUTION.map });
}

export function imageryTiles(): L.TileLayer {
  return L.tileLayer(IMAGERY_TILES, { maxZoom: 19, attribution: ATTRIBUTION.imagery });
}

/** 地图 / 卫星两种底图；「地图」由 `.is-dark-base` 压成暗色 */
export function basemapTiles(kind: "map" | "satellite"): L.TileLayer {
  return kind === "satellite" ? imageryTiles() : mapTiles();
}
