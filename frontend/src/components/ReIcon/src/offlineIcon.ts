// 这里存放本地图标，在 src/layout/index.vue 文件中加载，避免在首启动加载
import { getSvgInfo } from "@pureadmin/utils";
import { addIcon } from "@iconify/vue/dist/offline";

// https://icon-sets.iconify.design/ep/?keyword=ep
import EpHomeFilled from "~icons/ep/home-filled?raw";

// https://icon-sets.iconify.design/ri/?keyword=ri
import RiSearchLine from "~icons/ri/search-line?raw";
import RiInformationLine from "~icons/ri/information-line?raw";
import Team from "~icons/ri/team-line?raw";
import History from "~icons/ri/history-line?raw";
import Radar from "~icons/ri/radar-line?raw";
import Book from "~icons/ri/book-open-line?raw";
import Repeat from "~icons/ri/repeat-line?raw";
import Sword from "~icons/ri/sword-line?raw";
import Settings from "~icons/ri/settings-3-line?raw";
import Rules from "~icons/ri/file-list-3-line?raw";
import Group from "~icons/ri/group-line?raw";
import Command from "~icons/ri/command-line?raw";
import Ban from "~icons/ri/forbid-line?raw";
import Equalizer from "~icons/ri/equalizer-line?raw";
import Shield from "~icons/ri/shield-check-line?raw";
import Vip from "~icons/ri/vip-crown-line?raw";
import Config from "~icons/ri/file-settings-line?raw";
import Health from "~icons/ri/heart-pulse-line?raw";
import Paint from "~icons/ri/paint-brush-line?raw";
import Question from "~icons/ri/question-line?raw";

const icons = [
  // Element Plus Icon: https://github.com/element-plus/element-plus-icons
  ["ep/home-filled", EpHomeFilled],
  // Remix Icon: https://github.com/Remix-Design/RemixIcon
  ["ri/search-line", RiSearchLine],
  ["ri/information-line", RiInformationLine],
  ["ri/team-line", Team],
  ["ri/history-line", History],
  ["ri/radar-line", Radar],
  ["ri/book-open-line", Book],
  ["ri/repeat-line", Repeat],
  ["ri/sword-line", Sword],
  ["ri/settings-3-line", Settings],
  ["ri/file-list-3-line", Rules],
  ["ri/group-line", Group],
  ["ri/command-line", Command],
  ["ri/forbid-line", Ban],
  ["ri/equalizer-line", Equalizer],
  ["ri/shield-check-line", Shield],
  ["ri/vip-crown-line", Vip],
  ["ri/file-settings-line", Config],
  ["ri/heartbeat-line", Health],
  ["ri/paint-brush-line", Paint],
  ["ri/question-line", Question]
];

// 本地菜单图标，后端在路由的 icon 中返回对应的图标字符串并且前端在此处使用 addIcon 添加即可渲染菜单图标
icons.forEach(([name, icon]) => {
  addIcon(name as string, getSvgInfo(icon as string));
});
