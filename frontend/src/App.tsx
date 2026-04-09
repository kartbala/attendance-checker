import { useState } from 'react';
import { RegisterForm } from './components/RegisterForm';
import { AttendanceView } from './components/AttendanceView';

const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:5001';

type Screen = 'register' | 'attendance';

function App() {
  const [screen, setScreen] = useState<Screen>('register');
  const [email, setEmail] = useState('');

  const handleRegistered = (registeredEmail: string) => {
    setEmail(registeredEmail);
    setScreen('attendance');
  };

  const handleLookup = (lookupEmail: string) => {
    setEmail(lookupEmail);
    setScreen('attendance');
  };

  const handleBack = () => {
    setScreen('register');
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
          apiUrl={API_URL}
          onBack={handleBack}
        />
      )}
    </div>
  );
}

export default App;
