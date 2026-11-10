import test from 'node:test';
import assert from 'node:assert/strict';
import {notificationBody,desktopNotificationsEnabled,notificationMessages} from '../src/notifications.js';

test('compact off-page notifications preserve session targets and deduplicate active messages',()=>{
 const state={sessions:[{id:'active',messages:[{id:'same',role:'assistant',via:'text',text:'Full body'},{id:'chat-only',role:'assistant',via:'chat'}]}],notificationMessages:[{id:'same',sessionId:'active',role:'assistant',via:'text'},{id:'off-page',sessionId:'older-chat',role:'assistant',via:'text',text:'Finished'},{id:'wrong-role',sessionId:'other',role:'user',via:'text'}]};
 assert.deepEqual(notificationMessages(state),[{id:'same',sessionId:'active',role:'assistant',via:'text'},{id:'off-page',sessionId:'older-chat',role:'assistant',via:'text',text:'Finished'}]);
 assert.equal(notificationMessages({sessions:state.sessions})[0].text,'Full body','older full snapshots remain supported');
 assert.equal(notificationBody(notificationMessages(state)[1],{preview:false}),'Your Amplifier response is ready.');
});
test('desktop notifications respect opt-out and never include response text without preview opt-in',()=>{
 const message={text:'Private work details'};
 assert.equal(desktopNotificationsEnabled({desktop:false}),false);
 assert.equal(desktopNotificationsEnabled({desktop:true}),true);
 assert.equal(notificationBody(message,{}),'Your Amplifier response is ready.');
 assert.equal(notificationBody(message,{preview:false}),'Your Amplifier response is ready.');
 assert.equal(notificationBody(message,{preview:true}),'Private work details');
});

test('loading an old chat cannot create notifications outside the authoritative window',()=>{
 const sessions=[{id:'old-chat',messages:[{id:'old-reply',role:'assistant',via:'text',text:'An old response'}]}];
 assert.deepEqual(notificationMessages({sessions,notificationMessages:[]}),[]);
 assert.deepEqual(notificationMessages({sessions,notificationMessages:[{id:'new-reply',sessionId:'other-chat',role:'assistant',via:'text'}]}),[{id:'new-reply',sessionId:'other-chat',role:'assistant',via:'text'}]);
});
