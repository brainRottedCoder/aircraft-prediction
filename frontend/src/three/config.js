// Static 3D configuration: fault labels and damage-zone layout of the GLB models, camera view presets.

// Camera presets: [yaw, pitch]
export const VIEW_TARGETS = { top: [0, 1.2], side: [0, 0.04], under: [0, -1.2] };

// Fault messages for the three sub-components of each subsystem.
export const FMSG=['Tank seam leak','Boost pump A low pressure','Shut-off valve sticking'];
export const SFM={hyd:['Pump low pressure','Servo actuator sticking','Hose leak'],gear:['Tyres worn or low pressure','Strut seal leaking','Retract actuator slow'],radar:['Antenna elements failing','Gimbal drive error','Transmitter overheating']};
// Damage zones along an axis of the model (gear, radar): ax = axis index, z = [from, to, subcomponent], an = halo anchors, hr = halo radius.
export const SFG={gear:{ax:1,z:[[0,.33,0],[.33,.68,1],[.68,1.01,2]],an:[[0,.08,0],[0,.27,0],[0,.46,0]],hr:.085},radar:{ax:0,z:[[0,.3,2],[.3,.8,1],[.8,1.01,0]],an:[[.215,0,0],[.12,0,0],[.03,0,0]],hr:.06}};
