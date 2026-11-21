var cpQ_W     = cp.Q_W != null ? cp.Q_W : (cp.heatLoad != null ? cp.heatLoad : 350.0);
                    var cpR_th    = cp.R_th != null ? cp.R_th : 0.03;
                    var cpDpKpa   = cp.dp_rated_kPa != null ? cp.dp_rated_kPa : 10;
                    var cpQlpm    = cp.Q_rated_LPM != null ? cp.Q_rated_LPM : 1.0;
                    var cpCsolid  = cp.C_solid != null ? cp.C_solid : 100;
                    var cpVml     = cp.V_fluid_mL != null ? cp.V_fluid_mL : 5;

                    var cpUA      = 1.0 / cpR_th;
                    var cpRho     = fluid.rho != null ? fluid.rho : 997.0;
                    var cpMrated  = cpQlpm * cpRho / 60000.0;
                    var cpDpPa    = cpDpKpa * 1000.0;
                    var cpDpA     = cpMrated > 0 ? cpDpPa / (cpMrated * cpMrated) : 5e4;

                    return {
                            dp_a:            cpDpA,
                            dp_b:            0,
                            UA_W_per_K:      cpUA,
                            C_solid_J_per_K: cpCsolid,
                            V_fluid_m3:      cpVml * 1e-6,
                            Q_W:             cpQ_W
                        };