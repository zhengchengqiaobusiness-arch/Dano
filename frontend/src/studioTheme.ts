import { theme, type ThemeConfig } from "antd";

export const STUDIO_THEME: ThemeConfig = {
  algorithm: theme.darkAlgorithm,
  token: {
    colorPrimary: "#3d9e90",
    colorInfo: "#3d9e90",
    colorSuccess: "#3f8f6b",
    colorWarning: "#c4923a",
    colorError: "#c45c5c",
    colorBgBase: "#0c1016",
    colorBgContainer: "#121820",
    colorBgElevated: "#161d26",
    colorBgLayout: "#0c1016",
    colorBorder: "#1e2833",
    colorBorderSecondary: "#1a222c",
    colorText: "#c5cdd6",
    colorTextSecondary: "#8b96a3",
    colorTextTertiary: "#6b7682",
    colorFillSecondary: "#1a222c",
    borderRadius: 6,
    fontSize: 13,
  },
  components: {
    Table: {
      headerBg: "#161c26",
      headerColor: "#9aa5b1",
      rowHoverBg: "#182028",
      borderColor: "#1e2833",
      cellPaddingBlock: 6,
      cellPaddingInline: 12,
    },
    Switch: {
      handleBg: "#9aa5b1",
      colorPrimary: "#3d9e90",
      colorPrimaryHover: "#4aafa0",
    },
    Slider: {
      handleColor: "#9aa5b1",
      railBg: "#1e2833",
      trackBg: "#3d9e90",
    },
    Card: {
      colorBgContainer: "#121820",
      colorBorderSecondary: "#1e2833",
    },
    Button: {
      defaultBg: "#182028",
      defaultBorderColor: "#2a3542",
      defaultColor: "#c5cdd6",
    },
    Input: {
      colorBgContainer: "#0e141c",
      colorBorder: "#2a3542",
    },
    Alert: {
      colorInfoBg: "#13241f",
      colorInfoBorder: "#1f3d34",
    },
    Steps: {
      colorText: "#9aa5b1",
    },
  },
};
