# Team Player — Beyond the Usual Positions, Beyond Goals and Assists

Modern football values team players whose contribution goes beyond their own goals and assists. By moving away from their usual positions, players can support teammates and create opportunities that their individual statistics may never fully capture.

These movements may reflect a player’s style or a coach’s tactical instructions. A striker drops deep to connect play. A fullback joins the attack but leaves space behind. A winger runs without the ball to draw defenders away.

Such decisions can benefit the team while reducing the player’s own scoring opportunities. They may also benefit both the player and the team. Of course, all actions on the pitch can create disadvantages, these decisions may also expose the team to danger.

--------------------------------
| Movement | Potential benefit | Potential cost |
|---|---|---|
| **Striker drops deep** | Connects midfield with attack and finds teammates further forward. | May not return in time to receive a delivery or finish an attack. |
| **Fullback advances** | Provides an extra attacking option, creates overloads or reaches a scoring position. | Leaves space that opponents can exploit on the counterattack. |
| **Winger makes an off-ball decoy run** | Draws defenders away and creates space for teammates. | May remove a useful passing option without creating an advantage. |
--------------------------------

However, common football statistics, like goals, assists, possession, running distance and xG, cannot fully explain what these positional decisions give the team, or what they cost.

**Team Player makes those contributions visible.** Alongside positional changes and passing options, the prototype calculates *personal gain* and *shared team gain* to estimate the benefits and costs of movements away from usual positions. *These measurements update online as the match streams.* *Microsoft Foundry AI* turns the evidence into concise explanations for *audiences with different levels of football knowledge.* Narratives are generated *real-time* during playback, with only a couple of seconds delay.

**The interface shows an animated match on the left and AI explanations on the right. In AI explanations section, viewers can follow what is happening now and explore a similar historical situation in which a player made a different choice. An AI explained head-to-head view compares players in the same position across different teams, helping viewers examine differences in their observed playing styles. Linked glossary entries explain the football terms mentioned in the narratives to ensure transmissibility.**

The pipeline follows five stages:

1. **Ingest:** Generate synthetic matches and stream player and ball positions frame by frame. Positions are the only match observations supplied to the analyser.

2. **Interpret:** Infer possession, passes, shots and tactical movements. Measure passing options, positional opportunities, and the benefits and costs associated with the selected player’s movement.

3. **Explain:** Microsoft Foundry AI explains why a meaningful moment matters. **Multiple AI agents are recruited to perform different roles.** An AI editor checks whether the evidence is informative enough to show, while numerical checks help keep explanations grounded.

4. **Render:** Display the moving players and ball alongside concise narratives, contribution figures and links to supporting definitions.

5. **Personalise:** Viewers have the freedom to follow a selected player and compare players of interest across teams with personalised and real-time adjustable AI explanations.

All matches are synthetic. Twenty fictional teams follow the Premier League’s 38-matchweek, 380-fixture structure. Historical recordings from 2021–22 are analysed offline and stored in Azure Blob Storage. Current-season 2026–27 recordings are kept separate and analysed online frame by frame during playback, *without future observations or access to the simulator’s personality labels.*

The length of matches are set to be ten minutes for demonstration and better user experience. Playback defaults to 2x. *Choosing 1x gives AI explanations more time to arrive close to the action they describe.*

Viewers can watch five selected examples or generate a new reproducible ten-minute match through the website. Azure Container Apps hosts the viewer and generation worker. AI explanations are generated during playback, not prerecorded.

The prototype focuses on strikers, fullbacks and wingers. Its estimates describe modelled contributions and observed associations, not proven causal effects. For fans, broadcasters and analysts, the aim is simple: **appreciate team players' contributions, beyond the usual positions, beyond goals and assists.**

[Try the application](https://ms-pl-hackathon-team-player.redisland-fd1d12ca.northeurope.azurecontainerapps.io/), [Source code](https://github.com/yz753/ms_pl_hackathon_team_player)

Some limitations: this is more about a demonstration of the feasibility of my idea instead of a 100% complete pipeline to analyse the stats I mentioned. For example, the simulator is a simple designed model which is not able to replicate elite footballer's performance.
