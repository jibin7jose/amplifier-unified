import {useEffect,useMemo} from 'react';
import {createCanvasReloadGuard} from './canvas-reload-guard.js';
export function useCanvasReloadGuard(){
 const owner=useMemo(()=>createCanvasReloadGuard(),[]);
 useEffect(()=>owner.mount(),[owner]);
 return owner;
}
