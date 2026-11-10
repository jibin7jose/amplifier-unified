import React from 'react';
import './working-label.css';

// Reserve both labels' space so an asynchronous action does not move controls.
export function WorkingLabel({active,children,working}){
 return <span className="a-working-label"><span style={{visibility:active?'hidden':undefined}}>{children}</span><span style={{visibility:active?undefined:'hidden'}}>{working}</span></span>;
}
