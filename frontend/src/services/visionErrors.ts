// 视觉模型（Qwen3-VL / Ollama）错误码的中文解释。
// 目的：界面上不要只显示 m4d_model_unavailable 这种内部码，否则「模型没输出」看起来像模型坏了。
const HINTS: Record<string, string> = {
  m4d_model_unavailable:
    "视觉模型服务连不上：多半天是本机 Ollama 没在跑，请在宿主执行 ollama serve（并设 OLLAMA_HOST=0.0.0.0:11434）",
  m4d_model_http_error: "视觉模型服务返回了错误状态码：检查 Ollama 日志，确认模型名与 VISION_BASE_URL 一致",
  m4d_model_thinking_only: "模型只输出了推理过程（thinking 变体）：换用 *-instruct 变体",
  m4d_model_empty_response: "模型返回了空内容：可重试，或调大 num_predict / 降低温度",
  m4d_model_bad_response: "模型输出不是约定的 JSON：可重试，或改写提示词后重试",
  m4d_prompt_too_long: "提示词超过模型上下文长度：缩短自定义提示词",
  m4d_roi_too_small: "框选的区域太小：重新框选更大的范围",
  m4d_source_decode_failed: "图片解码失败：换一张图片重试",
  m4d_invalid_frame_size: "图片尺寸不合法（过小或过大）",
  m4d_encode_failed: "图片编码失败：换一张图片重试",
  m4d_invalid_prompt: "提示词不合法（空提示词）",
};

export function visionErrorText(code?: string | null): string {
  if (!code) return "未知原因";
  const hint = HINTS[code];
  return hint ? `${hint}（${code}）` : code;
}