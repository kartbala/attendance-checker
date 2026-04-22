import { useState, useEffect } from 'react';
import { RegisterForm } from './components/RegisterForm';
import { AttendanceView } from './components/AttendanceView';
import { Dashboard } from './components/Dashboard';

const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:5001';

type Screen = 'register' | 'attendance' | 'dashboard';

function readHashScreen(): Screen {
  return window.location.hash === '#dashboard' ? 'dashboard' : 'register';
}

function App() {
  const [screen, setScreen] = useState<Screen>(readHashScreen());
  const [email, setEmail] = useState('');
  const [courseCode, setCourseCode] = useState<string | undefined>();

  useEffect(() => {
    const onHash = () => setScreen(readHashScreen());
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
  }, []);

  const goRegister = () => { window.location.hash = ''; setScreen('register'); };

  const handleRegistered = (registeredEmail: string) => {
    setEmail(registeredEmail);
    setCourseCode(undefined);
    setScreen('attendance');
  };

  const handleLookup = (lookupEmail: string) => {
    setEmail(lookupEmail);
    setCourseCode(undefined);
    setScreen('attendance');
  };

  const handleBack = () => {
    setCourseCode(undefined);
    goRegister();
  };

  return (
    <div className="min-h-screen bg-gray-100 flex flex-col items-center p-4 py-8">
      {screen === 'register' && (
        <RegisterForm
          onRegistered={handleRegistered}
          onLookup={handleLookup}
          apiUrl={API_URL}
        />
      )}
      {screen === 'attendance' && (
        <AttendanceView
          email={email}
          courseCode={courseCode}
          onCourseSelect={setCourseCode}
          apiUrl={API_URL}
          onBack={handleBack}
        />
      )}
      {screen === 'dashboard' && (
        <Dashboard apiUrl={API_URL} onBack={goRegister} />
      )}
    </div>
  );
}

export default App;
