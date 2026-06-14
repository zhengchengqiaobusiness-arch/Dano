import alibabaSvg from "@lobehub/icons-static-svg/icons/alibaba.svg?raw";
import cerebrasSvg from "@lobehub/icons-static-svg/icons/cerebras.svg?raw";
import claudeSvg from "@lobehub/icons-static-svg/icons/claude.svg?raw";
import cohereSvg from "@lobehub/icons-static-svg/icons/cohere.svg?raw";
import deepseekSvg from "@lobehub/icons-static-svg/icons/deepseek.svg?raw";
import fireworksSvg from "@lobehub/icons-static-svg/icons/fireworks.svg?raw";
import googleSvg from "@lobehub/icons-static-svg/icons/google.svg?raw";
import groqSvg from "@lobehub/icons-static-svg/icons/groq.svg?raw";
import kimiSvg from "@lobehub/icons-static-svg/icons/kimi.svg?raw";
import metaSvg from "@lobehub/icons-static-svg/icons/meta.svg?raw";
import mistralSvg from "@lobehub/icons-static-svg/icons/mistral.svg?raw";
import openaiSvg from "@lobehub/icons-static-svg/icons/openai.svg?raw";
import perplexitySvg from "@lobehub/icons-static-svg/icons/perplexity.svg?raw";
import replicateSvg from "@lobehub/icons-static-svg/icons/replicate.svg?raw";
import togetherSvg from "@lobehub/icons-static-svg/icons/together.svg?raw";
import xaiSvg from "@lobehub/icons-static-svg/icons/xai.svg?raw";
import xiaomimimoSvg from "@lobehub/icons-static-svg/icons/xiaomimimo.svg?raw";
import zhipuSvg from "@lobehub/icons-static-svg/icons/zhipu.svg?raw";

const svgByProvider: Record<string, string> = {
  anthropic: claudeSvg,
  openai: openaiSvg,
  google: googleSvg,
  meta: metaSvg,
  mistral: mistralSvg,
  groq: groqSvg,
  kimi: kimiSvg,
  xai: xaiSvg,
  deepseek: deepseekSvg,
  cohere: cohereSvg,
  perplexity: perplexitySvg,
  together: togetherSvg,
  fireworks: fireworksSvg,
  replicate: replicateSvg,
  cerebras: cerebrasSvg,
  alibaba: alibabaSvg,
  zhipu: zhipuSvg,
  xiaomimimo: xiaomimimoSvg,
};

const providerAliases: Record<string, string> = {
  bedrock: "anthropic",
  vertex: "google",
  gemini: "google",
  azure: "openai",
  grok: "xai",
  amazon: "anthropic",
  claude: "anthropic",
  zai: "zhipu",
  "kimi-coding": "kimi",
  xiaomi: "xiaomimimo",
};

export function getProviderSvg(provider: string): string | undefined {
  const normalized = provider.toLowerCase().trim();
  const mapped = providerAliases[normalized] ?? normalized;
  return svgByProvider[mapped];
}
