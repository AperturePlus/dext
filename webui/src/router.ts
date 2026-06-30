import { createRouter, createWebHistory } from 'vue-router'
import OverviewPage from './pages/OverviewPage.vue'
import DataQualityPage from './pages/DataQualityPage.vue'
import TopologyPage from './pages/TopologyPage.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'overview', component: OverviewPage },
    { path: '/data', name: 'data', component: DataQualityPage },
    { path: '/topology', name: 'topology', component: TopologyPage },
    // Unknown paths fall back to the overview (KISS: no separate 404 page).
    { path: '/:pathMatch(.*)*', redirect: '/' }
  ]
})

export default router
