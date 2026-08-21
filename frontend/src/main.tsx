import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./styles.css";
import "./styles-release.css";
import "./styles-atlas.css";
import "./styles-templates.css";
import "./styles-ask.css"; // 问筑听界面重构预览层,未提交;删除本行即还原
import "./styles-tabs.css"; // 选项卡动效预览层,未提交;删除本行即还原
import "./tabMotion"; // 选项卡滑动指示器引擎,未提交;删除本行即还原
import "./styles-premium.css"; // 高端产品化打磨预览层,未提交;删除本行即还原
import "./styles-atlas-dark.css"; // 暗夜 Atlas 重制预览层,未提交;删除本行即还原纸本
import "./styles-room.css"; // 会议室打磨预览层,未提交;删除本行即还原
import "./docTypography"; // 纪要文档排版引擎,未提交;删除本行即还原

createRoot(document.getElementById("root")!).render(<React.StrictMode><App /></React.StrictMode>);
