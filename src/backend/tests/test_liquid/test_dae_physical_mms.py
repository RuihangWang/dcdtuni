"""Time refinement of the actual coupled CP + two-fluid HX equation graph.

The reference trajectory and source are hand-derived below. They do not call
the production residual to manufacture forcing or use its output as truth.
"""
import math
import json
import torch
from backend.tests.test_liquid.test_equation_graph import adapter,exact_state
from backend.liquidcooling.solver.dae_newton import backward_euler_step,solve_steady


def manufactured(eq,t):
    w=.2
    secondary=300+2*math.sin(w*t)
    primary=290+math.cos(w*t)
    ds=2*w*math.cos(w*t);dds=-2*w*w*math.sin(w*t)
    dp=-w*math.sin(w*t)
    conductance=.2*4000*(1-math.exp(-100/(.2*4000)))
    qcp=4000*ds+500*(secondary-primary)
    solid=secondary+qcp/conductance
    dsolid=ds+(4000*dds+500*(ds-dp))/conductance
    power=1000*dsolid+qcp
    primary_inlet=primary+(8000*dp-500*(secondary-primary))/(.1*4000)
    theta=eq.parameters(heat_W=torch.tensor([power],dtype=torch.float64),
                        primary_inlet_K=torch.tensor([primary_inlet],dtype=torch.float64))
    exact=exact_state(eq,theta,secondary=secondary,primary_return=primary,solid=solid)
    return theta,exact


def test_coupled_physical_mms_refines_at_backward_euler_order():
    eq=adapter();errors=[]
    for n in [6,12,24]:
        _,y=manufactured(eq,0.)
        for step in range(n):
            t=3.*(step+1)/n
            theta,expected=manufactured(eq,t)
            y,report=backward_euler_step(eq.problem,y,theta,time=t,dt=3./n)
            assert report.scaled_residual_inf<1e-8
        errors.append(float((y[eq.differential_mask]-expected[eq.differential_mask]).abs().max()))
    orders=[math.log(a/b,2) for a,b in zip(errors,errors[1:])]
    print('DAE_MMS_TIME '+json.dumps({'dt_s':[.5,.25,.125],'errors_K':errors,'orders':orders}))
    assert all(.85<value<1.15 for value in orders), {'errors_K':errors,'orders':orders}


def test_pipe_spatial_mms_is_separate_from_time_refinement():
    from backend.liquidcooling.schema import LiquidrunConfig
    from backend.liquidcooling.graphcompiling.compiler import compile_graph
    from backend.liquidcooling.solver.equation_graph import EquationGraph,PrimaryCircuit
    mass=.01;rho=1000.;cp=4000.;mu=.001;diameter=.05;length=1.;roughness=1e-6
    area=math.pi*diameter**2/4
    # Independent scalar Darcy friction evaluation sets a known flow root;
    # flow is still solved, never prescribed in the DAE state.
    magnitude=math.sqrt(mass**2+1e-16)
    reynolds=magnitude*diameter/(area*mu)
    laminar=64/(reynolds+1e-8)
    turbulent=.25/(math.log10(roughness/diameter/3.7+5.74/(reynolds+1e-8)**.9)+1e-10)**2
    blend=1/(1+math.exp(-(reynolds-2300)/200))
    dp=((1-blend)*laminar+blend*turbulent)*length/diameter/(2*rho*area**2)*mass*magnitude
    errors=[]
    for n in [8,16,32]:
        raw={'version':'1.0','source':'spatial-mms','projectName':'pipe-mms','gravity':[0,-9.81,0],
             'fluid':{'rho_kg_m3':rho,'cp_J_kgK':cp,'mu_Pa_s':mu,'k_W_mK':.6},
             'time':{'dt_s':1.,'N_t':2,'bdf_order':1},
             'nodes':[{'id':'in','type':'pressure_bc','position_m':[0,0,0],'bc':{'P_Pa':150000+dp,'T_K':295.}},
                      {'id':'out','type':'pressure_bc','position_m':[1,0,0],'bc':{'P_Pa':150000.}}],
             'edges':[{'id':'pipe','type':'pipe','from':'in','to':'out','params':{
                 'D_m':diameter,'roughness_m':roughness,'K_minor_per_bend':0.,'n_cells':n,
                 'waypoints_m':[[0,0,0],[1,0,0]]}}]}
        graph=compile_graph(LiquidrunConfig.model_validate(raw),device='cpu')
        centers=(torch.arange(n,dtype=torch.float64)+.5)/n
        # T*(x)=295+2*x^2; source integrates m*cp*dT*/dx over each cell.
        graph.cell_heat_W=(mass*cp*4*centers/n)[:,None].expand(n,2)
        empty=torch.empty(0,dtype=torch.float64)
        primary=PrimaryCircuit(empty,empty,empty,empty,empty,empty,'no HX in pipe MMS')
        eq=EquationGraph(graph,primary);theta=eq.parameters()
        y,info=solve_steady(eq.problem,eq.initial_guess(theta),theta)
        fields=eq.unpack(y,theta)
        assert abs(fields['flow'].item()-mass)<1e-8
        assert info.scaled_residual_inf<1e-8
        errors.append(float((fields['water_temperature']-(295+2*centers**2)).abs().max()))
    orders=[math.log(a/b,2) for a,b in zip(errors,errors[1:])]
    print('DAE_MMS_SPACE '+json.dumps({'cells':[8,16,32],'errors_K':errors,'orders':orders}))
    assert all(.9<value<1.1 for value in orders),{'errors_K':errors,'orders':orders}
