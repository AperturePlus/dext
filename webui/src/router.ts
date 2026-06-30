import { createRouter, createWebHistory } from 'vue-router'
import DashboardPage from './pages/DashboardPage.vue'
import TopologyPage from './pages/TopologyPage.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'dashboard', component: DashboardPage },
    { path: '/topology', name: 'topology', component: TopologyPage },
    // Unknown paths fall back to the dashboard (KISS: no separate 404 page).
    { path: '/:pathMatch(.*)*', redirect: '/' }
  ]
})

export default router
