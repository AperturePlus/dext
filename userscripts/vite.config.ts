import { defineConfig } from 'vite';
import monkey from 'vite-plugin-monkey';

export default defineConfig({
  plugins: [
    monkey({
      entry: 'src/main.ts',
      userscript: {
        name: 'Dexter',
        namespace: 'https://github.com/AperturePlus/dext',
        version: '1.0.0',
        description: 'Human-assisted crawler frontend',
        match: ['*://*/*'],
        exclude: ['*://dx.scu.edu.cn/*', '*://mail.scu.edu.cn/*'],
        noframes: true,
        'run-at': 'document-idle',
        grant: [
          'GM_xmlhttpRequest',
          'GM_addStyle',
          'GM_setValue',
          'GM_getValue',
          'GM_listValues',
          'GM_deleteValue',
          'GM.xmlHttpRequest',
          'GM.setValue',
          'GM.getValue',
          'GM.listValues',
          'GM.deleteValue',
        ],
        connect: ['127.0.0.1', 'localhost'],
      },
      build: {
        fileName: 'yanclaw-assistant.user.js',
      },
    }),
  ],
});
