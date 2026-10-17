
import ReactDOM from 'react-dom/client';
import {QueryClient,QueryClientProvider} from '@tanstack/react-query';
import {RootErrorBoundary} from '@/app/RootErrorBoundary';
import {ThemeProvider} from '@/shared/theme/ThemeProvider';
import {TooltipProvider} from '@/shared/ui/tooltip';
import {EmojiBurstProvider} from '@/shared/ui/EmojiBurstProvider';
import {PoofBurstProvider} from '@/shared/ui/PoofBurstProvider';
import {Toaster} from '@/shared/ui/sonner';
import {installErrorToasts} from '@/local/ErrorNotes';
import {createRootRoute,createRoute,createRouter,RouterProvider} from '@tanstack/react-router';
import {CommunitiesProvider} from '@/features/communities/useCommunities';
import {ConnectedApp} from '@/local/ConnectedApp';
import {StartupGate} from '@/local/StartupGate';
import {WorkspaceDraftGate} from '@/local/WorkspaceDraftGate';
import '@fontsource-variable/inter/opsz.css';
import '@fontsource-variable/inter/opsz-italic.css';
import '@fontsource/jetbrains-mono/400.css';
import '@fontsource/jetbrains-mono/700.css';
import '@/shared/styles/globals.css';
import {initializeConversationDensityPreference} from '@/shared/lib/conversationDensityPreference';
import {initializeFontSizePreference} from '@/shared/lib/fontSizePreference';
import {initDraftStore} from '@/features/messages/lib/useDrafts';
// Native startup selects a private WebView store for disposable data overrides
// before loading this module. Keep these keys unchanged to preserve everyday drafts.
// Local ownership matches the single-user host, not a provider identity.
initDraftStore('local-user','dervo-local');
initializeConversationDensityPreference();initializeFontSizePreference();installErrorToasts();
// staleTime 1 s: a query read less than a second ago isn't read again because another component mounted with the same key
// (opening a thread read task_detail twice and thread_folder three times). The host answers one request at a time, so each
// repeat made the next click wait. Polling and invalidateQueries are unaffected.
const queryClient=new QueryClient({defaultOptions:{queries:{retry:false,refetchOnWindowFocus:false,staleTime:1000}}});
const rootRoute=createRootRoute();
const localRoute=createRoute({getParentRoute:()=>rootRoute,path:'/',component:()=> <StartupGate><WorkspaceDraftGate><ConnectedApp/></WorkspaceDraftGate></StartupGate>});
const router=createRouter({routeTree:rootRoute.addChildren([localRoute]),defaultNotFoundComponent:()=> <main className="p-8">This destination isn't available locally. <a href="/">Return to Dervo</a></main>});
ReactDOM.createRoot(document.getElementById('root')!).render(<RootErrorBoundary><QueryClientProvider client={queryClient}><ThemeProvider defaultTheme="buzz"><TooltipProvider><EmojiBurstProvider><PoofBurstProvider><CommunitiesProvider><RouterProvider router={router}/></CommunitiesProvider><Toaster/></PoofBurstProvider></EmojiBurstProvider></TooltipProvider></ThemeProvider></QueryClientProvider></RootErrorBoundary>);
