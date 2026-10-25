<template>
  <section class="panel">
    <h2>独立业务地图 <small>仅供 M0 展示</small></h2>
    <div ref="mapRoot" class="map" role="img" aria-label="离线演示地图，无真实设备数据"></div>
    <p class="muted">本地图属于 TrajNexa 主系统，不复用 TrajNexa_Map 编辑页面。暂无真实地图数据，标记为模拟位置。</p>
  </section>
</template>
<script setup lang="ts">
import { ref, onMounted, onBeforeUnmount } from 'vue'
import L from 'leaflet'
const mapRoot = ref<HTMLElement | null>(null)
let map: L.Map | undefined
onMounted(() => {
  if (!mapRoot.value) return
  map = L.map(mapRoot.value, { zoomControl: true, attributionControl: false }).setView([25.273, 110.213], 12)
  L.circleMarker([25.273, 110.213], { radius: 8, color: '#31a68b', fillOpacity: .75 })
    .addTo(map).bindPopup('模拟点位 / 不代表真实摄像头')
})
onBeforeUnmount(() => map?.remove())
</script>
