import { createRoot } from 'react-dom/client'
import { ConfigProvider } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import { App } from './workspace'
import './workspace.css'

createRoot(document.getElementById('root')!).render(
  <ConfigProvider locale={zhCN} theme={{ token: {
    colorPrimary: '#087b6a',
    borderRadius: 10,
    fontFamily: '"Instrument Sans", "PingFang SC", "Microsoft YaHei", sans-serif',
  } }}>
    <App />
  </ConfigProvider>,
)
