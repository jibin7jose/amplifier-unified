import React from 'react';
import {ModelControl,useNextMessageConfiguration} from './chat-controls';
import {BundleControl} from './bundle-controls';

/** One quiet model/effort entry below the field at every conversation width. */
export function ComposerOptions({state,session,act,working,detail}){
 // One dialog owns one acknowledged revision chain across its model and bundle.
 // External client revisions still cannot replace a touched form's captured one.
 const nextMessageControl=useNextMessageConfiguration(session,act);
 const props={state,session,act,working,nextMessageControl};
 return <ModelControl {...props} compact detail={detail}>
  <BundleControl {...props} compact embedded/>
 </ModelControl>;
}
