import { createRouter, createWebHistory } from 'vue-router'
import OverviewPage from './pages/OverviewPage.vue'
import DataPage from './pages/DataPage.vue'
import QualityPage from './pages/QualityPage.vue'
import TopologyPage from './pages/TopologyPage.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'overview', component: OverviewPage },
    { path: '/data', name: 'data', component: DataPage },
    { path: '/quality', name: 'quality', component: QualityPage },
    { path: '/topology', name: 'topology', component: TopologyPage },
    // Unknown paths fall back to the overview (KISS: no separate 404 page).
    { path: '/:pathMatch(.*)*', redirect: '/' }
  ]
})

export default router
