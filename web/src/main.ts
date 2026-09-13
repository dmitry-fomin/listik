import { createApp } from 'vue'
import '@fontsource-variable/geist'
import '@fontsource-variable/jetbrains-mono'
import '@zoloto585/facet/tokens.css'
import '@zoloto585/facet/themes/amber.css'
import '@zoloto585/facet/themes/malachite.css'
import '@zoloto585/facet/themes/garnet.css'
import '@zoloto585/facet/themes/sapphire.css'
import '@zoloto585/facet/styles/utilities.css'
import './assets/app.css'
import App from './App.vue'
import { initTheme } from './lib/theme'

initTheme()

createApp(App).mount('#app')
