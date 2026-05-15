import { createApp } from "vue";
import { createPinia } from "pinia";
import "./style.css";
import App from "./App.vue";
import router from "./router";

import { library } from "@fortawesome/fontawesome-svg-core";
import { FontAwesomeIcon } from "@fortawesome/vue-fontawesome";

import {
  faArrowDown,
  faArrowLeft,
  faBars,
  faCamera,
  faCheck,
  faComments,
  faDoorOpen,
  faEye,
  faEyeSlash,
  faFloppyDisk,
  faGauge,
  faGear,
  faLock,
  faLockOpen,
  faMoon,
  faPaperPlane,
  faPlus,
  faRightFromBracket,
  faRightToBracket,
  faSun,
  faTriangleExclamation,
  faUser,
  faXmark,
} from "@fortawesome/free-solid-svg-icons";

library.add(
  faArrowDown,
  faArrowLeft,
  faBars,
  faCamera,
  faCheck,
  faComments,
  faDoorOpen,
  faEye,
  faEyeSlash,
  faFloppyDisk,
  faGauge,
  faGear,
  faLock,
  faLockOpen,
  faMoon,
  faPaperPlane,
  faPlus,
  faRightFromBracket,
  faRightToBracket,
  faSun,
  faTriangleExclamation,
  faUser,
  faXmark,
);

createApp(App)
  .use(createPinia())
  .use(router)
  .component("font-awesome-icon", FontAwesomeIcon)
  .mount("#app");
