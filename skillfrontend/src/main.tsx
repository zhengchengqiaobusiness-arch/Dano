import React from "react";
import ReactDOM from "react-dom/client";
import { ConfigProvider } from "antd";
import zhCN from "antd/locale/zh_CN";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import ErrorBoundary from "./ErrorBoundary";
import { routerBasename } from "./adminBase";
import { STUDIO_THEME } from "./studioTheme";
import "antd/dist/reset.css";
import "./studio.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ConfigProvider locale={zhCN} theme={STUDIO_THEME}>
      <BrowserRouter basename={routerBasename()}>
        <ErrorBoundary>
          <App />
        </ErrorBoundary>
      </BrowserRouter>
    </ConfigProvider>
  </React.StrictMode>,
);
