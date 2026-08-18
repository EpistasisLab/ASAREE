import { Navigate, Route, Routes } from 'react-router-dom'
import { ProtectedRoute } from '@/components/ProtectedRoute'
import { LoginPage } from '@/pages/LoginPage'
import { RegisterPage } from '@/pages/RegisterPage'
import { ProfilePage } from '@/pages/ProfilePage'
import { ExperimentsPage } from '@/pages/ExperimentsPage'
import { ProtocolCanvasPage } from '@/pages/ProtocolCanvasPage'

export function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/register" element={<RegisterPage />} />
      <Route element={<ProtectedRoute />}>
        <Route path="/profile" element={<ProfilePage />} />
        <Route path="/experiments" element={<ExperimentsPage />} />
        {/* There is no separate static experiment detail page anymore -- the
            protocol canvas (with its Design/Cells/Runs/Results side panel) IS
            the experiment view. The bare route stays as a redirect so old
            bookmarks and back-history entries land somewhere real instead of
            falling through to the catch-all. `to` is relative, so it resolves
            against this route's own matched path. */}
        <Route path="/experiments/:experimentId" element={<Navigate to="protocol" replace />} />
        <Route path="/experiments/:experimentId/protocol" element={<ProtocolCanvasPage />} />
      </Route>
      <Route path="/" element={<Navigate to="/experiments" replace />} />
      <Route path="*" element={<Navigate to="/experiments" replace />} />
    </Routes>
  )
}
