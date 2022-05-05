function [ypred,C] = nbClassify(sigPower,labels,isNorm)
for iTest = 1:length(labels)
    testId = iTest;
    trainId = setdiff(1:length(labels),iTest);
    XTrain = sigPower(trainId,:);
    XTest = sigPower(testId,:);
    YTrain = labels(trainId);
    YTest = labels(testId);
    if(isNorm)
    mTrain = mean(XTrain,1);
    sTrain = std(XTrain,0,1);
    XTrainNorm = (XTrain-mTrain)./sTrain;
    XTestNorm = (XTest - mTrain)./sTrain;
    else
        XTrainNorm = XTrain;
        XTestNorm = XTest;
    end
    fingerMdl=fitcnb(XTrainNorm,YTrain,'prior','uniform');
    ypred(iTest) = predict(fingerMdl,XTestNorm);
    
end
end