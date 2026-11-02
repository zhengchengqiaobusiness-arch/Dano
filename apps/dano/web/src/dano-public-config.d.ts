declare module "virtual:dano-public-config" {
  const config: { productName: string; shortcutLinks: readonly import("@dano/types/protocol").BridgeShortcutLinkConfig[] };
  export default config;
}
