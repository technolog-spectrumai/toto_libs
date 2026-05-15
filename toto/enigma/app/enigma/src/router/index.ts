import { createRouter, createMemoryHistory } from "vue-router";
import EnigmaWelcomeView from "../views/EnigmaWelcomeView.vue";
import EnigmaChatView from "../views/EnigmaChatView.vue";
import EnigmaConfigView from "../views/EnigmaConfigView.vue";
import EnigmaProfileView from "../views/EnigmaProfileView.vue";
import EnigmaLoginView from "../views/EnigmaLoginView.vue";

const router = createRouter({
  history: createMemoryHistory(),
  routes: [
    { path: "/", component: EnigmaWelcomeView, name: "welcome" },
    { path: "/login", component: EnigmaLoginView, name: "login" },
    { path: "/chat", component: EnigmaChatView, name: "chat" },
    { path: "/config", component: EnigmaConfigView, name: "config" },
    { path: "/profile", component: EnigmaProfileView, name: "profile" },
  ],
});

export default router;
