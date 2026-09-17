export function resolveProductName(environmentName, configuredName) {
  const productName = environmentName?.trim() || configuredName?.trim();
  if (!productName) {
    throw new Error(
      "Set productName in dano.config.json or provide DANO_PRODUCT_NAME",
    );
  }
  // Names are public identity, never template expressions or terminal controls.
  if (/[{}]|<%|%>|\x00|[\x01-\x1f\x7f]/u.test(productName)) {
    throw new Error("PRODUCT_NAME_INVALID");
  }
  return productName;
}

