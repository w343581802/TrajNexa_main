<script setup lang="ts">
import { computed } from "vue";

const props = withDefaults(
  defineProps<{
    time?: string;
    label?: string;
    tone?: "color" | "gray";
    shape?: "wide" | "portrait";
    chrome?: boolean;
    quality?: "good" | "low";
  }>(),
  { time: "2026-10-25 20:12:08", label: "CAM-01 东门入口 01", tone: "color", shape: "wide", chrome: true, quality: "good" },
);

const viewBox = computed(() => (props.shape === "portrait" ? "104 26 132 204" : "0 0 320 240"));
</script>

<template>
  <div class="mock-frame" :class="[`tone-${tone}`, `shape-${shape}`]">
    <svg :viewBox="viewBox" preserveAspectRatio="xMidYMid slice" aria-label="示例画面（占位，不代表真实录像）">
      <defs>
        <linearGradient id="mf-sky" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stop-color="#1a2836" />
          <stop offset="0.55" stop-color="#111c27" />
          <stop offset="1" stop-color="#0a1118" />
        </linearGradient>
        <radialGradient id="mf-lamp" cx="0.5" cy="0.5" r="0.5">
          <stop offset="0" stop-color="#ffd79a" stop-opacity="0.5" />
          <stop offset="1" stop-color="#ffd79a" stop-opacity="0" />
        </radialGradient>
        <radialGradient id="mf-vig" cx="0.5" cy="0.45" r="0.72">
          <stop offset="0.55" stop-color="#000" stop-opacity="0" />
          <stop offset="1" stop-color="#000" stop-opacity="0.55" />
        </radialGradient>
        <linearGradient id="mf-floor" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stop-color="#16202b" />
          <stop offset="1" stop-color="#0b1219" />
        </linearGradient>
      </defs>

      <rect width="320" height="240" fill="url(#mf-sky)" />

      <!-- 远景街区 -->
      <g fill="#0c141d">
        <rect x="-4" y="104" width="76" height="62" />
        <rect x="76" y="86" width="48" height="80" />
        <rect x="128" y="112" width="34" height="54" />
        <rect x="232" y="78" width="92" height="88" />
      </g>
      <g fill="#5c7f9e" opacity="0.28">
        <rect x="8" y="116" width="6" height="5" />
        <rect x="22" y="116" width="6" height="5" />
        <rect x="36" y="130" width="6" height="5" />
        <rect x="86" y="98" width="6" height="5" />
        <rect x="100" y="98" width="6" height="5" />
        <rect x="86" y="114" width="6" height="5" />
        <rect x="246" y="92" width="7" height="5" />
        <rect x="262" y="92" width="7" height="5" />
        <rect x="278" y="108" width="7" height="5" />
        <rect x="262" y="124" width="7" height="5" />
      </g>

      <!-- 地面与道路 -->
      <rect y="150" width="320" height="90" fill="url(#mf-floor)" />
      <path d="M0 240 122 152 202 152 320 240Z" fill="#101923" />
      <path d="M122 152h80" stroke="#2a3a4a" stroke-width="1.5" />
      <g stroke="#3b5265" stroke-width="2" stroke-dasharray="10 13" opacity="0.5">
        <path d="M56 240 158 152" />
        <path d="M258 240 166 152" />
      </g>

      <!-- 路灯 -->
      <g stroke="#3a4d5f" stroke-width="2" fill="none">
        <path d="M46 172v-58" />
        <path d="M46 114h16" />
      </g>
      <circle cx="66" cy="115" r="16" fill="url(#mf-lamp)" />
      <circle cx="66" cy="115" r="2.6" fill="#f6d79f" />

      <!-- 远处行人 -->
      <g opacity="0.55">
        <circle cx="118" cy="146" r="3.6" fill="#8fa0b2" />
        <path d="M115 151h6l2 14h-10z" fill="#4b5b6d" />
        <path d="M116 165l-1 11h3l1-11zM119 165l1 11h3l-1-11z" fill="#384656" />
      </g>

      <!-- 主要人物 -->
      <g>
        <ellipse cx="182" cy="216" rx="27" ry="5" fill="#000" opacity="0.38" />
        <circle cx="182" cy="88" r="9.6" fill="#cbd5df" />
        <path d="M172 87a10 10 0 0 1 20 0z" fill="#2a3543" />
        <path d="M169 100q13-7 26 0l4 45q-17 7-34 0z" fill="#3a4a5d" />
        <path d="M169 100q13-7 26 0l1 8q-14-4-28 0z" fill="#44566a" opacity="0.7" />
        <path d="M196 106h9q4 0 4 4v15h-13z" fill="#252f3a" />
        <path d="M196 106q6-9 13 0" stroke="#252f3a" stroke-width="2.2" fill="none" />
        <path d="M174 146l-3 53h10l4-53z" fill="#1e2833" />
        <path d="M188 146l6 53h10l-7-53z" fill="#25303c" />
        <path d="M169 199h12v4h-13z" fill="#59697a" />
        <path d="M194 199h12v4h-13z" fill="#59697a" />
      </g>

      <rect width="320" height="240" fill="url(#mf-vig)" />
      <rect width="320" height="240" fill="none" stroke="rgba(255,255,255,0.05)" stroke-width="2" />
    </svg>

    <div v-if="chrome" class="mf-chrome">
      <span class="mf-cam">{{ label }}</span>
      <span class="mf-rec"><i />REC</span>
      <span class="mf-time num">{{ time }}</span>
      <span v-if="quality === 'low'" class="mf-quality">画质不足 · 不可增强</span>
    </div>
  </div>
</template>

<style scoped>
.mock-frame {
  position: relative;
  width: 100%;
  height: 100%;
  overflow: hidden;
  background: #0a1118;
  border-radius: inherit;
}

.mock-frame svg {
  width: 100%;
  height: 100%;
  display: block;
}

.tone-gray svg {
  filter: grayscale(0.86) contrast(0.92) brightness(1.06);
}

.mf-chrome {
  position: absolute;
  inset: 0;
  pointer-events: none;
}

.mf-cam,
.mf-rec,
.mf-time,
.mf-quality {
  position: absolute;
  font-family: var(--mono);
  font-size: 8px;
  letter-spacing: 0.02em;
  color: rgba(232, 241, 250, 0.78);
  text-shadow: 0 1px 2px rgba(0, 0, 0, 0.8);
  white-space: nowrap;
}

.mf-cam {
  top: 5px;
  left: 6px;
  max-width: 62%;
  overflow: hidden;
  text-overflow: ellipsis;
}

.mf-rec {
  top: 5px;
  right: 6px;
  display: inline-flex;
  align-items: center;
  gap: 3px;
  color: rgba(255, 164, 158, 0.9);
  font-size: 7px;
}

.mf-rec i {
  width: 4px;
  height: 4px;
  border-radius: 50%;
  background: #ff6b63;
  animation: blink 2.4s infinite;
}

.mf-time {
  bottom: 4px;
  right: 6px;
  font-size: 7.5px;
}

.mf-quality {
  bottom: 4px;
  left: 6px;
  font-size: 7px;
  color: rgba(240, 180, 90, 0.85);
}

.shape-portrait .mf-time,
.shape-portrait .mf-cam {
  font-size: 6px;
}
</style>
